#!/bin/bash
#PBS -N Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet
#PBS -l nodes=1:ppn=12
#PBS -o Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet.pbs.out
#PBS -e Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet.inp > Rubredoxin_minus1_quartet_sextet_interp_007_lam_0.6667_mult_6_sextet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
