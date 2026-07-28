#!/bin/bash
#PBS -N Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet
#PBS -l nodes=1:ppn=12
#PBS -o Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet.pbs.out
#PBS -e Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet.inp > Qu_sex_nagative_1_interp_002_lam_0.1111_mult_6_sextet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
